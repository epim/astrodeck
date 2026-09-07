// viewBoundaryDom.test.tsx — ViewBoundary's two failure panes (ViewBoundary.tsx).
//
// The bug this guards against: every render throw used to land on the SAME
// "Couldn't load this screen ... didn't finish downloading" pane as a genuine
// chunk-fetch failure — which is how a null-magnitude catalog row (the NGC 604
// incident) got reported to the user as a WiFi drop. `isChunkLoadError`
// classifies the caught error; a chunk failure still gets the download pane and
// a Reload button (retrying an import() that failed once is worthless — see the
// file header), and everything else gets an honest "This screen hit an error"
// pane carrying the thrown message, plus a Try again that resets in place.
//
// Run directly:  npx tsx src/components/__tests__/viewBoundaryDom.test.tsx

/* eslint-disable @typescript-eslint/no-explicit-any */

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
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "requestAnimationFrame",
  "cancelAnimationFrame", "getComputedStyle", "matchMedia",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, lazy, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { __setLazyComponentForTest } = await import("../../lib/lazyViews");
const { isChunkLoadError } = await import("../ViewBoundary");
const ViewBoundary = (await import("../ViewBoundary")).default;

// React logs every caught render error to console.error by design (dev mode);
// both failures under test are deliberate, so silence it rather than let two
// expected stack traces read as a broken test run.
const realConsoleError = console.error;
console.error = () => {};

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** A fresh container + root each time, so a `key`-stable re-render of
 *  ViewBoundary can never carry the previous case's caught-error state. */
function mountRoot(): { container: any; root: ReturnType<typeof createRoot> } {
  const container = win.document.createElement("div");
  win.document.body.appendChild(container);
  return { container, root: createRoot(container) };
}

// -------------------------------------------------------- isChunkLoadError
await test("a webpack-style ChunkLoadError (by .name) is a chunk failure", () => {
  const e = new Error("Loading chunk 3 failed.");
  e.name = "ChunkLoadError";
  assert(isChunkLoadError(e), "not classified as a chunk failure");
});
await test("native import() rejection messages are chunk failures", () => {
  assert(isChunkLoadError(new Error("Loading chunk 3 failed.")), "Loading chunk");
  assert(isChunkLoadError(new Error("Failed to fetch dynamically imported module: /assets/GuideView.js")),
    "Failed to fetch dynamically imported");
  assert(isChunkLoadError(new Error("error loading dynamically imported module")),
    "dynamically imported module");
  assert(isChunkLoadError(new Error("Importing a module script failed.")),
    "Importing a module script failed");
});
await test("a genuine render throw is NOT a chunk failure", () => {
  assert(!isChunkLoadError(new TypeError("Cannot read properties of null (reading 'toFixed')")),
    "a TypeError was classified as a download failure");
  assert(!isChunkLoadError(new Error("something else broke")), "an unrelated Error");
});
await test("a non-Error thrown value is never a chunk failure", () => {
  assert(!isChunkLoadError("a string"), "string");
  assert(!isChunkLoadError(null), "null");
  assert(!isChunkLoadError(undefined), "undefined");
});

// --------------------------------------------------------- the rendered panes
// `mount` is a real entry in lazyViews' LOADERS table; __setLazyComponentForTest
// pins its cached lazy component to one built for this test instead of the real
// chunk loader, so neither case below touches the network.

await test("a chunk-load failure keeps today's download message and a Reload button", async () => {
  class FakeChunkLoadError extends Error {
    override name = "ChunkLoadError";
  }
  __setLazyComponentForTest("mount", lazy(() =>
    Promise.reject(new FakeChunkLoadError("Loading chunk 3 failed."))));

  const { container, root } = mountRoot();
  await act(async () => { root.render(createElement(ViewBoundary, { view: "mount" })); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });

  const text = container.textContent || "";
  assert(/Couldn't load this screen/.test(text), `wrong pane shown: ${text}`);
  assert(/didn't finish downloading/.test(text), `download explanation missing: ${text}`);
  const reload = [...container.querySelectorAll("button")]
    .find((b: any) => /Reload app/.test(b.textContent || ""));
  assert(reload != null, "no Reload app button on the download-failure pane");
  assert(!/This screen hit an error/.test(text), "the render-error pane leaked in too");

  await act(async () => { root.unmount(); });
});

await test("a render throw shows the honest pane with the thrown message, and Try again", async () => {
  function Boom(): never {
    throw new TypeError("Cannot read properties of null (reading 'toFixed')");
  }
  __setLazyComponentForTest("mount", lazy(() => Promise.resolve({ default: Boom })));

  const { container, root } = mountRoot();
  await act(async () => { root.render(createElement(ViewBoundary, { view: "mount" })); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });

  const text = container.textContent || "";
  assert(/This screen hit an error/.test(text), `wrong pane shown: ${text}`);
  assert(/rest of the app keeps working/.test(text), `reassurance line missing: ${text}`);
  assert(text.includes("Cannot read properties of null (reading 'toFixed')"),
    `the thrown message is not on screen: ${text}`);
  assert(!/didn't finish downloading/.test(text), "the chunk-failure pane leaked in instead");

  const buttons = [...container.querySelectorAll("button")];
  assert(buttons.some((b: any) => /Reload app/.test(b.textContent || "")),
    "no Reload app button on the render-error pane");
  const tryAgain = buttons.find((b: any) => /Try again/.test(b.textContent || ""));
  assert(tryAgain != null, "no Try again button on the render-error pane");

  await act(async () => { root.unmount(); });
});

// ------------------------------------------------------------------- report
console.error = realConsoleError;
const total = passed + failed;
console.log(`viewBoundaryDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
