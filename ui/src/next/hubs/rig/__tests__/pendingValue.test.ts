// pendingValue.test.ts - the mount tracking latch (plan hub-rig.md 0.4 shape 2).
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/pendingValue.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The three ways a pending value must die are the whole subject: adopted by the
// rig, reverted by a refusal, expired by the clock. A latch that only ever does
// the first is the defect this file exists to prevent - a value on screen that
// no hardware agrees with, kept there by the UI itself.
//
// It renders through a probe component rather than testing a "pure" function
// because the expiry lives in an effect: the timeout is the behaviour, and a
// version of this test that only called the reducer would pass over a latch
// with no `setTimeout` in it at all.

/* eslint-disable @typescript-eslint/no-explicit-any */

const { JSDOM } = await import("jsdom");
const dom = new JSDOM(`<!doctype html><html><body><div id="root"></div></body></html>`, {
  url: "http://local/", pretendToBeVisual: true,
});
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { usePendingValue } = await import("../lib/pendingValue");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

// The probe publishes the hook's whole return through a module-level handle, so
// a test can drive it exactly the way a control does.
type Latch = ReturnType<typeof usePendingValue<string>>;
let latch: Latch | null = null;
function Probe({ actual, timeoutMs }: { actual: string | undefined; timeoutMs: number }) {
  latch = usePendingValue<string>(actual, timeoutMs);
  return createElement("span", { "data-testid": "v" }, String(latch.value ?? "-"));
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const shown = () => (container.textContent || "").trim();

async function render(actual: string | undefined, timeoutMs = 40): Promise<void> {
  await act(async () => { root.render(createElement(Probe, { actual, timeoutMs })); });
}

await testAsync("precondition - the hook renders the rig's own value", async () => {
  await render("sidereal");
  eq(shown(), "sidereal", "the probe did not render the actual value at all");
  eq(latch?.pending, false, "a hook nobody has written to reports itself pending");
});

await testAsync("show() paints the chosen value before the rig answers", async () => {
  await render("sidereal");
  await act(async () => { latch!.show("lunar"); });
  eq(shown(), "lunar", "the chosen value did not paint - the control would sit unmoved for 2 s");
  eq(latch?.pending, true, "the latch does not report itself pending while a value is chosen");
});

await testAsync("the rig reporting the same thing hands control back", async () => {
  await render("sidereal");
  await act(async () => { latch!.show("lunar"); });
  await render("lunar");              // the 2 s status frame catches up
  await settle();
  eq(latch?.pending, false, "the latch stayed pending after the rig adopted the value");
  eq(shown(), "lunar", "the value changed when the rig confirmed it");
});

await testAsync("revert() drops a refused value at once", async () => {
  await render("sidereal");
  await act(async () => { latch!.show("lunar"); });
  eq(shown(), "lunar", "precondition: the chosen value is on screen");
  await act(async () => { latch!.revert(); });
  eq(shown(), "sidereal", "a refused POST left the chosen value on screen");
  eq(latch?.pending, false, "a reverted latch still reports itself pending");
});

await testAsync("a value the mount never adopts EXPIRES", async () => {
  // The one that matters most: without the timeout, a mount that silently
  // ignores the command leaves a number on screen that no hardware agrees with,
  // for as long as the sheet stays open.
  await render("sidereal", 20);
  await act(async () => { latch!.show("solar"); });
  eq(shown(), "solar", "precondition: the chosen value is on screen");
  await act(async () => { await new Promise((r) => setTimeout(r, 45)); });
  eq(shown(), "sidereal", "the latch never expired - it outlived the command it was showing");
  eq(latch?.pending, false, "an expired latch still reports itself pending");
});

await testAsync("an undefined actual is not a value", async () => {
  await render(undefined);
  eq(shown(), "-", "an absent reading rendered as something other than nothing");
  eq(latch?.pending, false, "a hook over an absent reading reports itself pending");
});

act(() => { root.unmount(); });

const total = passed + failed;
console.log(`pendingValue.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
