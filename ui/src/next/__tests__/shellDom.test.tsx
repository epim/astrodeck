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
// logs, monitor snapshot) answers 404 QUIETLY. The store's loaders all swallow a
// failure and keep the seeded value, so the fixture below is what the shell sees
// rather than a race with the network.
const asked: string[] = [];
g.fetch = async (url: any, init?: any) => {
  asked.push(`${init?.method ?? "GET"} ${String(url)}`);
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

// ------------------------------------------------------------- navigation

test("tapping WEATHER changes the hash AND the screen", () => {
  eq(byId("hub-sky") != null, true, "precondition: the sky hub is on screen");
  click(byId("tab-weather"));
  eq(win.location.hash, "#/weather/conditions", "hash:");
  assert(byId("hub-weather") != null, "the weather hub did not render");
  assert(byId("hub-sky") == null, "the sky hub is still mounted under the weather hub");
});

test("the sub-nav renders the hub's sections and switches them without a push", () => {
  const chips = all('[data-testid="subnav"] button');
  eq(chips.length, 3, "weather has three sections:");
  const radar = chips.find((c) => /RADAR/.test(c.textContent));
  assert(radar != null, "no RADAR chip");
  click(radar);
  eq(win.location.hash, "#/weather/radar");
});

test("switching hubs clears the sub back to the new hub's default", () => {
  click(byId("tab-monitor"));
  eq(win.location.hash, "#/monitor/live");
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
  act(() => { win.location.hash = "#/rig/devices/focuser"; });
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

test("a setView after mount routes through the bridge inside the real shell", () => {
  act(() => { useStore.getState().setView("monitor"); });
  eq(win.location.hash, "#/monitor/live", "hash:");
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
