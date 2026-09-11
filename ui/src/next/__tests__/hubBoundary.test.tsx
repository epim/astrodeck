// hubBoundary.test.tsx - the error boundary the new root did not have.
//
//   Run directly:  npx tsx src/next/__tests__/hubBoundary.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS GUARDS (review #2). `NextApp` mounted `<Hub />` bare and `SheetHost`
// mounted `<Comp />` bare, so ONE unguarded null anywhere under `hubs/**`
// blanked the ENTIRE app - header, tab bar, toasts and the brightness reset
// with it - with no message and no way back but a manual reload, on a phone, in
// the dark. `components/ViewBoundary.tsx` exists because the legacy root paid
// for exactly that once already (the NGC 604 night, a magnitude-less catalog
// row).
//
// The three things a test of a boundary has to prove, because the boundary
// LOOKS like it works as long as nothing throws:
//
//   1. The pane appears AND NAMES THE SCREEN. "This screen hit an error" does
//      not tell a user at the mount which of six hubs and thirty sheets broke.
//   2. TRY AGAIN genuinely re-mounts. A retry that re-renders the same failure
//      is a button that lies, which is this repo's dominant defect class.
//   3. A chunk failure gets RELOAD and NO retry. A dynamic import() whose fetch
//      fails is cached as a failure for the life of the document, so an in-page
//      retry can never reach the network - measured in `lib/lazyViews.ts`.
//
// SABOTAGE CHECKS (each names the assertion that goes red):
//   * delete `getDerivedStateFromError` -> every assertion here fails, because
//     the throw escapes and takes the container down with it.
//   * drop `name` from the failure titles -> "the pane names the screen".
//   * make `retry()` a no-op -> "TRY AGAIN re-mounts the subtree".
//   * classify everything as a render throw (drop `isChunkLoadError`) -> "a
//     failed chunk offers RELOAD and NOT a retry".
//   * remove the boundary from `SheetHost`'s Slot -> "a sheet that throws does
//     not take the sheet layer with it".

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// Some hub modules reached through the sheet registry import CSS. Node has no
// idea what a `.css` file is, so a synchronous load hook answers with an empty
// module - the same stub `shellDom.test.tsx` installs.
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
win.WebSocket = class { close() {} addEventListener() {} removeEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;
g.fetch = async () => ({
  ok: false, status: 404, statusText: "Not Found",
  headers: { get: () => "application/json" },
  json: async () => ({}), text: async () => "",
});

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { HubBoundary } = await import("../shell/HubBoundary");
const { SheetHost } = await import("../shell/SheetHost");
const { SHEETS } = await import("../hubs");

// React prints the caught error and its component stack to console.error. That
// is correct behaviour and pure noise here, so it is swallowed for the life of
// this file - the ASSERTIONS are what grade the boundary, not the console.
const realError = console.error;
console.error = () => {};

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}

const container = win.document.getElementById("root") as any;
const q = (sel: string) => container.querySelector(sel) as any;
const byId = (id: string) => q(`[data-testid="${id}"]`);
const click = (el: any) => {
  assert(el != null, "click: the element is not there - the fixture is wrong, not the component");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};

// --------------------------------------------------------------- the fixture
// One flag, so the SAME component can throw on one render and succeed on the
// next. That is what makes the retry assertion real: a boundary whose TRY AGAIN
// did nothing would still show the pane, and a test that only re-rendered a
// component which always throws could not tell the two apart.
let boom = true;
const THROWN = "cannot read magnitude of null";

function Body(): any {
  if (boom) throw new Error(THROWN);
  return createElement("span", { "data-testid": "hub-body-ok" }, "the real screen");
}

function ChunkBody(): any {
  // The message shape a native `import()` rejects with in Safari/Firefox, which
  // is what `isChunkLoadError` classifies as a download rather than a bug.
  throw new Error("Failed to fetch dynamically imported module: /assets/RigHub-abc.js");
}

const root = createRoot(container);

// A sibling OUTSIDE the boundary. If the boundary does not contain the throw,
// this disappears too - which is precisely the failure being closed.
function Shell({ name, child }: { name: string; child: any }): any {
  return createElement(
    "div",
    null,
    createElement("nav", { "data-testid": "chrome" }, "TAB BAR"),
    createElement(HubBoundary as any, { name }, createElement(child)),
  );
}

// ------------------------------------------------------------------ a throw

act(() => { root.render(createElement(Shell, { name: "RIG - CAPTURE", child: Body })); });

test("a hub that throws renders the failure pane instead of a blank body", () => {
  assert(byId("hub-render-failed") != null, "a throwing hub rendered nothing at all");
  assert(byId("hub-body-ok") == null, "precondition: the body did not render");
});

test("the pane names the screen", () => {
  const text = String(byId("hub-render-failed").textContent);
  assert(/RIG - CAPTURE HIT AN ERROR/.test(text),
    `the failure must name the screen the user is looking at, got "${text}"`);
});

test("the pane shows the thrown text verbatim, so a screenshot is a bug report", () => {
  const msg = byId("hub-error-message");
  assert(msg != null, "no message element in the failure pane");
  eq(String(msg.textContent), THROWN);
});

test("the rest of the app is still on screen", () => {
  assert(byId("chrome") != null,
    "the throw escaped the boundary and took the chrome with it - the whole point of #2");
});

test("a render throw offers a retry AND a reload", () => {
  assert(byId("hub-error-retry") != null, "no TRY AGAIN on a render failure");
  assert(byId("hub-error-reload") != null, "no RELOAD APP on a render failure");
});

// ------------------------------------------------------------------ recovery

test("TRY AGAIN re-mounts the subtree, and the screen comes back", () => {
  boom = false;                      // the state that threw is gone
  click(byId("hub-error-retry"));
  assert(byId("hub-render-failed") == null, "the failure pane survived its own TRY AGAIN");
  assert(byId("hub-body-ok") != null,
    "TRY AGAIN cleared the pane but never re-mounted the screen - a button that lies");
});

// ------------------------------------------------------------ a chunk failure

test("a failed chunk offers RELOAD and NOT a retry", () => {
  act(() => { root.render(createElement(Shell, { name: "SKY", child: ChunkBody })); });
  assert(byId("hub-chunk-failed") != null, "a chunk failure did not get its own pane");
  assert(byId("hub-error-reload") != null, "a chunk failure must offer a reload");
  eq(byId("hub-error-retry"), null,
    "a retry cannot reach the network after a failed import - the module map caches the failure:");
});

// --------------------------------------------------- a reused boundary resets

test("a DIFFERENT screen in a reused boundary does not inherit the failure", () => {
  boom = false;
  act(() => { root.render(createElement(Shell, { name: "SESSION - NOW", child: Body })); });
  assert(byId("hub-chunk-failed") == null, "the previous screen's failure pane is still up");
  assert(byId("hub-render-failed") == null, "the previous screen's failure pane is still up");
  assert(byId("hub-body-ok") != null, "the new screen never mounted");
});

// ------------------------------------------------------------- the sheet slot

test("a sheet that throws does not take the sheet layer with it", () => {
  // Injected through the registry's own shape: `SHEETS` is the plain object
  // `SheetHost` looks a name up in, so this is the same path a real sheet
  // takes rather than a mock of the host.
  (SHEETS as any).boomsheet = function BoomSheet(): any {
    throw new Error("a sheet threw");
  };
  const route = { hub: "rig", sub: "devices", sheets: ["boomsheet"], params: {} } as any;
  act(() => { root.render(createElement(SheetHost as any, { route, phone: true })); });

  assert(byId("sheet-layer") != null,
    "a throwing sheet took the whole sheet layer down - BACK is now unreachable");
  const pane = byId("hub-render-failed");
  assert(pane != null, "the throwing sheet rendered no failure pane");
  assert(/BOOMSHEET HIT AN ERROR/.test(String(pane.textContent)),
    `the sheet's failure must name the sheet, got "${String(pane.textContent)}"`);
});

act(() => { root.unmount(); });
console.error = realError;

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`hubBoundary.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
export { passed, failed, total };
