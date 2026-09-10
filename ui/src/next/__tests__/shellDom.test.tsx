// shellDom.test.tsx - NextApp MOUNTED: the chrome, the router, the hosts and
// the two gate screens.
//
//   Run directly:  npx tsx src/next/__tests__/shellDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS IS ABOUT. The shell is the one component every screen in the new UI
// renders inside, and each of its failures is invisible from a unit test of the
// pieces:
//
//   1. The six tabs exist AND carry accessible names. Five of the six render no
//      label (the design shows the active hub's label only), so `aria-current`
//      and `aria-label` are the whole of what a screen reader gets.
//   2. A tab tap changes the HASH and the SCREEN. Asserting only one of those
//      passes while the other is broken - a router that navigates to a hub the
//      registry does not render is a blank page with a correct URL.
//   3. A sheet opens from the hash, shows BACK, and BACK pops it. Sheets are
//      route state, so this is the browser Back button's behaviour too.
//   4. The toast stack and the confirm card read the SAME store slices the
//      legacy hosts read, so `confirmDialog()` from any reused component still
//      resolves. KEEP must resolve FALSE - a confirm whose cancel resolved true
//      would fire the destructive action it was asked to prevent.
//   5. The legacy `setView` bridge is live inside the real shell, not only in
//      its own isolated test.
//   6. NO LINK appears when the link is down. That chip is the only thing on a
//      Sky screen that says this view is not being told anything.
//   7. The login gate renders `views/Login` INSIDE the new chrome.
//
// Convention: jsdom by hand, createRoot + act, native events, printed tally plus
// the `{ passed, failed, total }` export (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// `NextApp` imports `next.css` and `shell/shell.css` - it is the single import
// site for both, by contract, so that the cascade order cannot depend on module
// resolution order. Node has no idea what a `.css` file is, so a synchronous
// load hook answers with an empty module. This is the ONLY way to keep both
// facts true at once: the app has one style entry point, and that entry point
// is still mountable in a test.
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

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

// Everything answers false, so the shell renders the PHONE layout - tab bar, no
// rail, no session column. That is the layout the design is written for.
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
// One socket that never opens: `connectWs()` runs for real (it is the shell's
// job), it just never receives anything.
win.WebSocket = class {
  static OPEN = 1;
  readyState = 0;
  close() {}
  send() {}
  addEventListener() {}
  removeEventListener() {}
};
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame",
  // `ws.ts` reads the BARE `location` (not `window.location`) to build the
  // socket URL, and `api.ts` does the same at module scope.
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// Every /api GET the shell fires (healthz, resume-arm, config, me, auth methods,
// logs, monitor snapshot) answers 404 QUIETLY, EXCEPT the handful the cross-hub
// chrome genuinely reads. The store's loaders all swallow a failure and keep the
// seeded value, so the fixture below is what the shell sees rather than a race
// with the network.
//
// The campaign fixture is the same shape `crossHub.test.ts` uses, because the
// strip renders from the SAME hook: `budget` is the ledger, and `banked_h: null`
// is "nobody has looked", not zero.
const FLOW_CARD = {
  id: "flow-m31", name: "M31 LRGB", folder: "My flows", tagline: "a campaign",
  readonly: false, stages: 9, wires: 8, last_run: 1_757_000_000,
  last_result: "ok", updated_ts: 1_757_000_500,
};
const SESSION_ROW = {
  id: "s-camp", name: "M31 LRGB", status: "dormant",
  created_ts: 1_756_000_000, updated_ts: 1_757_000_000,
  nights: 2, accepted: 41, total: 120, auto_resume: true,
};
const SESSION_FULL = {
  id: "s-camp", schema_version: 1, name: "M31 LRGB",
  created_ts: SESSION_ROW.created_ts, updated_ts: SESSION_ROW.updated_ts,
  status: "dormant", origin: "flow", origin_id: "flow-m31",
  nights: ["2026-09-07", "2026-09-08"], auto_resume: true,
  plan: {
    name: "M31 LRGB", cool_to: -10, cool_timeout_s: 600,
    targets: [{
      id: "t1", name: "M31", steps: [{
        id: "st-L", filter: "L", exposure_s: 120, count: 30,
        gain: 100, offset: 10, binning: 1, frame_type: "Light",
      }],
    }],
  },
  frames: [],
};
const TONIGHT = {
  ok: true, reason: "",
  night: {
    dusk_unix: 1_757_100_000, dawn_unix: 1_757_130_000,
    dark_start_unix: 1_757_103_600, dark_end_unix: 1_757_126_000,
  },
  budget: [
    { filter: "L", goal_h: 6, banked_h: 2.5, tonight_h: 1, has_ledger: true },
    { filter: "Ha", goal_h: 6, banked_h: null, tonight_h: 1, has_ledger: false },
  ],
};

const asked: string[] = [];
g.fetch = async (url: any, init?: any) => {
  const u = String(url);
  asked.push(`${init?.method ?? "GET"} ${u}`);
  const ok = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
    text: async () => JSON.stringify(data),
  });
  if (u === "/api/flows") return ok([FLOW_CARD]);
  if (u === "/api/flows/folders") return ok([{ name: "My flows", count: 1, readonly: false }]);
  if (u === "/api/flows/flow-m31/tonight") return ok(TONIGHT);
  if (u === "/api/sessions") return ok({ sessions: [SESSION_ROW] });
  if (u === "/api/sessions/s-camp") return ok(SESSION_FULL);
  if (u === "/api/reports") return ok([]);
  if (u === "/api/alerts/health") {
    return ok({ undelivered: 3, undelivered_by_sink: {}, deadman: { configured: false, healthy: true, last_ping_age_s: null } });
  }
  return {
    ok: false, status: 404, statusText: "Not Found",
    headers: { get: () => "application/json" },
    json: async () => ({}),
    text: async () => "",
  };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const { confirmDialog } = await import("../../components/ConfirmDialog");
const NextApp = (await import("../NextApp")).default;

// The six hub bodies are code-split (`hubs/index.ts`, review #43), so
// `HUBS[hub]` is a `React.lazy` whose module has to arrive before the body
// renders. Warm all six HERE, once, rather than letting each tab tap race a
// filesystem read inside `act()`: a lazy body that resolves on its own schedule
// is a flaky test, not an assertion. Every navigation below still goes through
// Suspense - it just resolves from the module registry.
const { HUB_LOADERS } = await import("../hubs");
await Promise.all(Object.values(HUB_LOADERS).map((load) => load()));

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
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}

const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
const q = (sel: string) => container.querySelector(sel) as any;
const all = (sel: string) => Array.from(container.querySelectorAll(sel)) as any[];
const byId = (id: string) => q(`[data-testid="${id}"]`);
const click = (el: any) => {
  assert(el != null, "click: the element is not there - the fixture is wrong, not the component");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};

// ------------------------------------------------------------------- fixture
const ALL_CAPS = [
  "view.status", "view.media", "view.weather", "view.site_precise", "view.site_derived",
  "control.capture", "control.mount", "control.guide", "control.power",
  "config.backend", "config.safety", "config.site_optics", "config.alerts",
  "admin.users", "system.update",
];

function seed(): void {
  useStore.setState({
    // Open LAN: no method enabled, an admin principal. The gate resolves to
    // "open" without a network round trip.
    authMethods: { methods: [], first_run: false } as any,
    principal: { role: "admin", email: null, caps: ALL_CAPS } as any,
    authGate: "open",
    wsPhase: "up",
    telemetryStale: false,
    equipConnected: true,
    toasts: [],
    confirm: null,
    view: "connect",
    helpTopic: null,
    lastReportId: null,
    weather: null,
    resumeArm: null,
    runBanner: null,
    status: {
      connected: {
        camera: { connected: true, name: "sim camera" },
        telescope: { connected: true, name: "sim mount" },
      },
      looping: false,
      mode: "sim",
      camera: { temperature: -10, can_cool: true, width: 100, height: 100, max_gain: 100 },
      mount: {
        ra_hours: 0, dec_deg: 0, ra_str: "00:00:00", dec_str: "+00:00:00",
        alt: 45, az: 90, tracking: true, parked: false, slewing: false,
      },
    } as any,
  } as never);
}

win.location.hash = "#/sky";
seed();

const root = createRoot(container);
await act(async () => { root.render(createElement(NextApp)); });
await settle();

// `connectWs()` really runs (it is the shell's job), and against a socket that
// never opens it leaves the phase at "connecting" - which is a LINK LOST
// incident and a NO LINK chip. Force the healthy state here so the tests that
// assert those two can put it back deliberately.
act(() => { useStore.setState({ wsPhase: "up", wsConnected: true } as never); });

// --------------------------------------------------------------- the chrome

test("the shell mounted and is not a gate screen", () => {
  assert(byId("next-app") != null, "no .nx-app root - the fixture is wrong, not the component");
  assert(byId("next-splash") == null, "the splash is up: the seeded authMethods should have resolved the gate");
  assert(byId("next-login") == null, "the login gate is up on an open-LAN fixture");
});

test("the wordmark renders", () => {
  const w = q(".nx-wordmark");
  assert(w != null, "no wordmark in the header");
  eq(w.textContent, "ASTRODECK");
});

test("six tabs, each with an accessible name", () => {
  const tabs = all('[data-testid="tabbar"] button');
  eq(tabs.length, 6, "tab count:");
  const names = tabs.map((t) => t.getAttribute("aria-label"));
  for (const n of ["SKY", "WEATHER", "SESSION", "RIG", "MONITOR", "SETTINGS"]) {
    assert(names.includes(n), `no tab named ${n}; got ${names.join(", ")}`);
  }
  // Five of six render no visible label, so the name is all a screen reader has.
  assert(names.every((n) => !!n), "a tab with no accessible name is an unlabelled icon");
});

test("the active hub is marked, and only it", () => {
  const current = all('[data-testid="tabbar"] button[aria-current="page"]');
  eq(current.length, 1, "exactly one aria-current tab:");
  eq(current[0].getAttribute("data-testid"), "tab-sky");
});

test("the rig chips carry NUMBERS, not just labels", () => {
  const cam = byId("header-cam");
  assert(cam != null, "no camera chip");
  assert(/-10/.test(cam.textContent), `the camera chip must show the temperature, got "${cam.textContent}"`);
  const mount = byId("header-mount");
  assert(/TRACK/.test(mount.textContent), `a tracking mount must say so, got "${mount.textContent}"`);
  assert(/SIM/.test(byId("header-backend")?.textContent ?? ""), "the sim backend must be named");
});

test("...and every one of them is short enough to survive a 390 px row", () => {
  // MEASURED (probe, 390x844): this row used to ellipsise all of it at once -
  // `CAM ...`, `MOUNT...`, `S...`, `D`. jsdom computes no widths, so the width
  // POLICY is guarded by `shellCss.test.ts` reading the stylesheet and the
  // measurement itself lives in a browser run. What is checkable here is the
  // budget those widths came from: monospace at 10 px is 6 px a character, and
  // the four readouts plus the control have 271 px of row at 390 px. A chip
  // whose visible text grows past this is one that will be clipped, silently,
  // on the width the design is drawn for.
  const budget: Array<[string, number]> = [
    // `CAM -10°` - the label, the sign, two digits and the degree.
    ["header-cam", 8],
    // `MOUNT` alone at phone: the state word is a separate element that
    // `shell.css` drops at `data-bp="phone"` (see shellFixes).
    ["header-mount", 5],
    // `SIM` / `NINA` / `ALPACA` / `NATIVE` - the widest backend name.
    ["header-backend", 6],
  ];
  for (const [id, max] of budget) {
    const el = byId(id);
    assert(el != null, `no ${id} chip`);
    const phone = id === "header-mount"
      ? el.textContent.replace(el.querySelector(".nx-mount-state")?.textContent ?? "", "")
      : el.textContent;
    assert(phone.length <= max,
      `${id} prints "${phone}" (${phone.length} chars, ~${phone.length * 6} px) - the ` +
        `390 px row budgets ${max}; a longer chip is one the row has to clip`);
  }
  // And the one control in the row prints nothing at all: its state is the
  // sun/moon glyph, and the words are in its accessible name.
  const night = byId("header-night");
  assert(night != null, "no night control in the header");
  eq(night.textContent, "", "the night toggle must be icon-only:");
});

// ------------------------------------------------------------- navigation

await testAsync("tapping WEATHER changes the hash AND the screen", async () => {
  eq(byId("hub-sky") != null, true, "precondition: the sky hub is on screen");
  click(byId("tab-weather"));
  eq(win.location.hash, "#/weather/conditions", "hash:");
  await settle();
  assert(byId("hub-weather") != null, "the weather hub did not render");
  assert(byId("hub-sky") == null, "the sky hub is still mounted under the weather hub");
});

await testAsync("the sub-nav renders the hub's sections and switches them without a push", async () => {
  const chips = all('[data-testid="subnav"] button');
  eq(chips.length, 3, "weather has three sections:");
  const radar = chips.find((c) => /RADAR/.test(c.textContent));
  assert(radar != null, "no RADAR chip");
  click(radar);
  await settle();
  eq(win.location.hash, "#/weather/radar");
  assert(byId("hub-weather") != null,
    "a sub-nav tap must not remount the hub away - it is looking around one screen");
});

await testAsync("switching hubs clears the sub back to the new hub's default", async () => {
  click(byId("tab-monitor"));
  eq(win.location.hash, "#/monitor/live");
  await settle();
  assert(byId("hub-monitor") != null, "the monitor hub did not render");
});

// ----------------------------------------------------------------- sheets

await testAsync("a sheet opens from the hash, with BACK", async () => {
  // Assigned directly, not through `nav`: this is the deep link / browser Back
  // path, where the only signal is the queued `hashchange`.
  act(() => { win.location.hash = "#/rig/devices/demo"; });
  await settle();
  assert(byId("sheet-layer") != null, "no phone sheet layer");
  const sheet = byId("sheet-demo");
  assert(sheet != null, "the registered demo sheet did not render");
  const back = sheet.querySelector(".nx-sheet-back");
  assert(back != null, "a sheet with no BACK is a screen with no way out");
  assert(/BACK/.test(back.textContent), `BACK pill text: got "${back.textContent}"`);
});

test("BACK pops the sheet and leaves the hub behind it", () => {
  click(q(".nx-sheet-back"));
  eq(win.location.hash, "#/rig/devices", "hash:");
  assert(byId("sheet-demo") == null, "the sheet is still on screen after BACK");
  assert(byId("hub-rig") != null, "the hub under the sheet did not come back");
});

await testAsync("a sheet name no hub registers says so instead of rendering nothing", async () => {
  // Was `focuser` until the Rig wave registered that sheet for real - which is
  // the case SheetHost's own MissingSheet comment anticipated. The assertion is
  // unchanged; only the example had to be a name that is still unregistered.
  act(() => { win.location.hash = "#/rig/devices/nosuchsheet"; });
  await settle();
  const missing = byId("sheet-missing");
  assert(missing != null, "an unbuilt sheet rendered a blank layer, which reads as a broken app");
  assert(/NOT BUILT YET/.test(missing.textContent), "the unbuilt sheet must say what happened");
  act(() => { win.location.hash = "#/sky"; });
  await settle();
});

// ------------------------------------------------------------------ toasts

test("a store enqueueToast renders in the new stack", () => {
  eq(byId("toasts"), null, "precondition: no toasts on screen");
  act(() => {
    useStore.getState().enqueueToast({ level: "warning", title: "the mount is parked" });
  });
  const stack = byId("toasts");
  assert(stack != null, "enqueueToast rendered nothing");
  assert(/the mount is parked/.test(stack.textContent), `toast text: got "${stack.textContent}"`);
  assert(/WARNING/.test(stack.textContent), "the level must be a WORD, not only a border colour");
});

test("dismissing a toast removes it", () => {
  const x = q('[data-testid="toasts"] .nx-toast-x');
  click(x);
  eq(byId("toasts"), null, "the toast survived its own dismiss button");
});

// ----------------------------------------------------------------- confirm

await testAsync("confirmDialog renders the card and KEEP resolves FALSE", async () => {
  eq(byId("confirm-scrim"), null, "precondition: no dialog on screen");
  let answer: boolean | null = null;
  let p!: Promise<boolean>;
  act(() => {
    p = confirmDialog({
      title: "Clear the horizon",
      body: "Every point drawn for this site is deleted.",
      confirmLabel: "CLEAR",
      tone: "danger",
    });
    void p.then((v) => { answer = v; });
  });
  const card = byId("confirm-scrim");
  assert(card != null, "confirmDialog rendered no card");
  assert(/Clear the horizon/.test(card.textContent), "the card did not render the title");
  assert(byId("confirm-yes") != null, "no destructive button");

  click(byId("confirm-keep"));
  await settle();
  eq(answer, false, "KEEP must resolve false - anything else fires the action it prevents:");
  eq(byId("confirm-scrim"), null, "the card stayed up after the answer");
});

// ---------------------------------------------------------- the legacy door

await testAsync("a setView after mount routes through the bridge inside the real shell", async () => {
  act(() => { useStore.getState().setView("monitor"); });
  eq(win.location.hash, "#/monitor/live", "hash:");
  await settle();
  assert(byId("hub-monitor") != null, "the monitor hub did not render");
});

// -------------------------------------------------------------- the link

test("wsPhase down raises the NO LINK chip", () => {
  eq(byId("header-link"), null, "precondition: the link is up and the chip is absent");
  act(() => { useStore.setState({ wsPhase: "down", wsConnected: false } as never); });
  const chip = byId("header-link");
  assert(chip != null, "no NO LINK chip while the link is down");
  assert(/NO LINK/.test(chip.textContent), `chip text: got "${chip.textContent}"`);
});

test("a down link also raises the banner that says the rig keeps going", () => {
  const banners = byId("banners");
  assert(banners != null, "no banner strip while the link is down");
  assert(/keeps running/.test(banners.textContent),
    `the banner must say the session survives the link, got "${banners.textContent}"`);
  act(() => { useStore.setState({ wsPhase: "up", wsConnected: true } as never); });
  eq(byId("header-link"), null, "the chip must clear when the link comes back");
});

// -------------------------------------------------- the cross-hub wiring

await testAsync("the flows pill reads the library and then stops asking", async () => {
  await settle();
  const pill = byId("header-flows");
  assert(pill != null, "no flows pill in the header");
  // The pill used to show a dash forever: nothing in the shell ever read the
  // library, so `libraryLoaded` stayed false under a rig with saved flows.
  assert(/1/.test(pill.textContent),
    `the pill must carry the count it fetched, got "${pill.textContent}"`);

  // And it is a LOAD, not a poll. A count on a chip is not worth a request
  // every N seconds all night on a field link, so the guard is what this
  // asserts: walking the hubs and letting the clock run adds no new reads.
  const before = asked.filter((a) => a === "GET /api/flows").length;
  click(byId("tab-rig"));
  await settle();
  click(byId("tab-sky"));
  await settle();
  eq(asked.filter((a) => a === "GET /api/flows").length, before,
    "the saved-flow library is read on mount and never re-read by the chrome:");
});

await testAsync("the campaign strip prints the night and the bank, and not on the screen it points at", async () => {
  act(() => {
    useStore.setState({
      resumeArm: {
        armed: {
          id: "s-camp", name: "M31 LRGB", owed: 58, accepted: 41, total: 99,
          origin: "flow", origin_id: "flow-m31",
        },
        hold: null,
      } as any,
      sequence: {
        state: "running", plan_name: "M31 LRGB",
        session: { id: "s-camp", name: "M31 LRGB", count_mode: "attempts", accepted: 41 },
      } as any,
    } as never);
  });
  await settle();

  const strip = byId("campaign-strip");
  assert(strip != null, "a running campaign has no strip on another hub");
  // The whole point of routing this through the Session hub's fold: the night
  // number and the banked hours are NOT on the live socket, and the strip used
  // to print "41 frames banked" because that is all `sequence.session` carries.
  assert(/night 2 of ~\d+/.test(strip.textContent),
    `the strip must name which night this is, got "${strip.textContent}"`);
  assert(/of 12 h banked/.test(strip.textContent),
    `and how much is in the bank, got "${strip.textContent}"`);

  act(() => { win.location.hash = "#/session/now"; });
  await settle();
  eq(byId("campaign-strip"), null, "a link to where you already are is not information");
});

await testAsync("an incident banners on every OTHER hub, and the Session tab wears its dot", async () => {
  act(() => { win.location.hash = "#/sky"; });
  await settle();
  act(() => {
    useStore.setState({
      // `sky.holding` rather than state "holding": the engine publishes the sky
      // verdict beside `state` on every publish precisely so a routine
      // `running` publish cannot hide a hold.
      sequence: {
        state: "running", plan_name: "M31 LRGB",
        session: { id: "s-camp", name: "M31 LRGB", count_mode: "attempts", accepted: 41 },
        sky: { cloudy: true, age_s: 30, score: 0.2, reason: "few stars", text: "4 stars", holding: true },
      } as any,
    } as never);
  });
  await settle();

  const banners = byId("banners");
  assert(banners != null, "a cloud hold on another hub must be announced");
  assert(/CLOUD HOLD/.test(banners.textContent),
    `the incident title leads the banner, got "${banners.textContent}"`);
  assert(byId("tab-session-dot") != null, "the Session tab must pulse while a hold is up");

  act(() => { win.location.hash = "#/session/now"; });
  await settle();
  const still = byId("banners");
  assert(still == null || !/CLOUD HOLD/.test(still.textContent),
    "on the Session hub the card is already on screen at full size");
  eq(byId("tab-session-dot"), null, "and the tab you are on does not need a dot");
});

await testAsync("a second incident turns the NOW chip into `NOW 2`", async () => {
  act(() => { win.location.hash = "#/session/gallery"; });
  await settle();
  const one = byId("subnav-now");
  assert(one != null, "no NOW chip on the session sub-nav");
  assert(!/2/.test(one.textContent), `precondition: one incident is the dot alone, got "${one.textContent}"`);

  act(() => {
    // The server's own low-disk flag, which is what the model reads now - not a
    // free-bytes threshold recomputed on the phone.
    useStore.setState({
      status: {
        ...(useStore.getState().status as any),
        disk: { free_gb: 1.2, low: true, critical: false },
      } as any,
    } as never);
  });
  await settle();

  const two = byId("subnav-now");
  assert(/2/.test(two.textContent),
    `a second card under the first is something the dot cannot say, got "${two.textContent}"`);
  assert(two.querySelector(".nx-subnav-dot") != null, "the chip keeps its dot as well as its count");
});

await testAsync("the weather chip dims for a stale feed and warns for an un-ignored alert", async () => {
  act(() => {
    useStore.setState({
      sequence: { state: "idle" } as any,
      status: { ...(useStore.getState().status as any), disk: undefined } as any,
      weather: {
        enabled: true, fetched_ts: 1, stale: true, ignore_tonight: false,
        threshold_pct: 70, sustain_minutes: 60, site_lat: null, site_lon: null,
        forecast: null, astrospheric: null, alert: null,
      } as any,
    } as never);
    win.location.hash = "#/weather/conditions";
  });
  await settle();
  const stale = byId("subnav-conditions")?.querySelector(".nx-subnav-dot");
  assert(stale != null, "a stale feed must show on the chip");
  eq(stale.getAttribute("data-tone"), "dim",
    "a stale feed is an absence of information, not bad news:");

  act(() => {
    useStore.setState({
      weather: {
        ...(useStore.getState().weather as any),
        stale: false,
        alert: {
          kind: "high_cloud", start_iso: "2026-09-10T22:00:00Z",
          end_iso: "2026-09-11T02:00:00Z", peak_pct: 92, dominant_layer: "high",
        },
      } as any,
    } as never);
  });
  await settle();
  eq(byId("subnav-conditions")?.querySelector(".nx-subnav-dot")?.getAttribute("data-tone"), "warn",
    "an un-overridden alert is amber:");

  act(() => {
    useStore.setState({
      weather: { ...(useStore.getState().weather as any), ignore_tonight: true } as any,
    } as never);
  });
  await settle();
  eq(byId("subnav-conditions")?.querySelector(".nx-subnav-dot"), null,
    "an alert the operator has already overridden is not still a warning");
});

await testAsync("the high-cloud dialog says what the engine actually does with a forecast", async () => {
  // The old last sentence claimed auto-resume would hold unless "ignore weather
  // tonight" was set. The engine's auto-resume gate is RAIN-ONLY and fail-open
  // (`server/astrodeck/weather.py` `veto_reason`), and cloud holds are measured
  // in-run from the rig's own frames. Telling an operator the forecast will hold
  // the night is how a clear night gets given away.
  act(() => {
    useStore.setState({
      weather: {
        ...(useStore.getState().weather as any),
        ignore_tonight: false,
        alert: {
          kind: "high_cloud", start_iso: "2026-09-10T22:00:00Z",
          end_iso: "2026-09-11T02:00:00Z", peak_pct: 92, dominant_layer: "high",
        },
      } as any,
      weatherAlertKey: 1,
    } as never);
  });
  await settle();

  const card = byId("confirm-scrim");
  assert(card != null, "the high-cloud notice never opened");
  const body = String(card.textContent);
  assert(/92% total cloud/.test(body), `the forecast peak is the news, got "${body}"`);
  assert(!/Auto-resume will hold/i.test(body),
    "the dialog still claims the forecast holds a run, which the engine does not do");
  assert(/does not\s+hold a run/i.test(body), `the correction must be stated, got "${body}"`);
  assert(/only forecast rain/i.test(body),
    "and it must name what DOES block an auto-resume");
  click(byId("confirm-ok"));
  await settle();
});

// ---------------------------------------------------------------- the gate

await testAsync("the login gate renders views/Login inside the new chrome", async () => {
  act(() => {
    useStore.setState({
      authMethods: { methods: ["local"], first_run: false } as any,
      principal: { role: "viewer", email: null, caps: [] } as any,
    } as never);
  });
  await settle();
  const login = byId("next-login");
  assert(login != null, "the login gate did not take over");
  assert(/Sign in to control the rig/.test(login.textContent),
    `views/Login did not render inside the chrome; got "${String(login.textContent).slice(0, 120)}"`);
  assert(login.querySelector(".nx-wordmark") != null, "the gate lost the wordmark above the sheet");
  // A sheet with nowhere to go back to must not offer BACK.
  assert(login.querySelector(".nx-sheet-back") == null,
    "the sign-in sheet offers a BACK that leads nowhere");
  assert(byId("tabbar") == null, "the tab bar is reachable behind the sign-in gate");
});

act(() => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`shellDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
