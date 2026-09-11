// appHeaderDom.test.tsx - the classic (`#/classic`) header strip, mounted for
// real, under a VIEWER-shaped status.
//
//   Run directly:  npx tsx src/__tests__/appHeaderDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS IS ABOUT. `server/astrodeck/api/redact.py` strips `mount.alt`/
// `mount.az` (`_MOUNT_DERIVED_KEYS`) from every status/summary/WS payload for a
// principal without `view.site_derived` - a plain viewer. `App.tsx`'s top
// status strip read `status.mount.alt.toFixed(0)` unconditionally, so a
// viewer's classic root threw during the very first render and white-screened
// at every width, on every view, because the header renders above all of them.
// This test mounts the real `App` component (not a fragment of it) with a
// viewer principal and a mount block that has `ra_str`/`dec_str`/`tracking`
// but NO `alt`/`az` - exactly the shape the server hands a viewer - and
// asserts the header renders (RA/Dec text is present) rather than throwing.
//
// Sabotage: reverting App.tsx's guard back to the bare
// `status.mount.alt.toFixed(0)` throws during `root.render`, which this
// harness's own `test()` wrapper catches as a failure - "the classic header
// rendered for a viewer without site-derived mount data" goes red.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// App.tsx pulls in `index.css` via main.tsx in the real app; App.tsx itself
// imports no CSS directly, but several of its children's transitive imports
// may. Node has no idea what a `.css` file is, so answer with an empty module
// the same way shellDom.test.tsx does.
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

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
// One socket that never opens - connectWs() runs for real (App's job), it
// just never receives anything.
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
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// Every /api GET App/EquipmentView fires answers 404 quietly (the same posture
// shellDom.test.tsx uses) - every loader here swallows a failure and keeps the
// seeded store value, so the fixture below is what the header sees rather than
// a race with the network.
g.fetch = async (_url: any, _init?: any) => ({
  ok: false, status: 404, statusText: "Not Found",
  headers: { get: () => "application/json" },
  json: async () => ({}),
  text: async () => "",
});

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../store");
const App = (await import("../App")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;

// ------------------------------------------------------------------- fixture
// A VIEWER's status.mount, exactly as `_redact_site_for` hands it back: RA/Dec
// stay (they say where the telescope looks, not where it stands), `alt`/`az`
// are ABSENT (not null, not zero - popped, per redact.py's `_strip_mount_derived`).
function seedViewer(): void {
  useStore.setState({
    authMethods: { methods: [], first_run: false } as any, // open LAN: never a login gate
    principal: { role: "viewer", email: null, caps: ["view.status", "view.preview"] } as any,
    wsPhase: "up",
    telemetryStale: false,
    equipConnected: false,
    view: "connect",
    runBanner: null,
    resumeArm: null,
    weather: null,
    sequence: { state: "idle" } as any,
    status: {
      connected: {},
      looping: false,
      mode: "sim",
      mount: {
        ra_hours: 1.5, dec_deg: 30, ra_str: "01:30:00", dec_str: "+30:00:00",
        tracking: true, parked: false, slewing: false,
        // alt / az deliberately absent - a viewer's payload has no such keys.
      },
    } as any,
  } as never);
}

win.location.hash = "#/classic";
seedViewer();

const root = createRoot(container);

// ---------------------------------------------------------------------- test

test("the classic header renders for a viewer with no site-derived mount data", () => {
  act(() => { root.render(createElement(App)); });
});

await settle();

test("the header shows the mount's RA/Dec (not hidden, not crashed)", () => {
  const header = container.querySelector(".app-header");
  assert(header != null, "no .app-header - the render did not reach the header at all");
  const text = String(header.textContent);
  assert(/01:30:00/.test(text) && /\+30:00:00/.test(text),
    `RA/Dec missing from the header, got "${text}"`);
  assert(!/ALT/.test(text),
    `the ALT span rendered without a numeric alt to show - got "${text}"`);
});

act(() => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`appHeaderDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
