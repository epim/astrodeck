// filterNamesModal.test.tsx — the learn-offsets Start gate, MOUNTED.
//
//   Run directly:  npx tsx src/components/capture/__tests__/filterNamesModal.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// #49: Start dimmed itself and kept the whole reason in a `title=`, which a
// fingertip never fires. On the tablet it was a pale button that depressed
// under a thumb, did nothing, and offered no way to ask why — while a fully
// working Reference picker sat above it. This asserts the three channels the
// house rule (§11.8) requires of a blocked control: it does not act, it SAYS
// why in visible text, and a press on it still answers.
//
// jsdom rather than a server render, because "a press answers" is a state
// change over time and the static tree cannot see one.

/* eslint-disable @typescript-eslint/no-explicit-any */

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
win.fetch = async () => ({
  ok: true, status: 200, headers: { get: () => "application/json" },
  json: async () => ({}), text: async () => "{}",
});

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "requestAnimationFrame", "cancelAnimationFrame",
  "getComputedStyle", "matchMedia", "WebSocket", "fetch",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { FilterNamesModal } = await import("../FilterNamesModal");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const NAMES = ["L", "R", "G", "B"];
const OFFSETS = [0, 12, -4, 7];
const BLOCKED = "a capture loop is running";
let learnCalls = 0;

const container = win.document.getElementById("root") as any;
const root = createRoot(container);

function render(reason: string | null): void {
  act(() => {
    root.render(createElement(FilterNamesModal, {
      open: true,
      onClose: () => {},
      names: NAMES,
      offsets: OFFSETS,
      position: 0,
      canLearn: true,
      learnDisabledReason: reason,
      onLearn: async () => { learnCalls++; },
      onSave: async () => {},
    }));
  });
}
const text = () => String(container.textContent ?? "");
const named = (label: string) =>
  [...container.querySelectorAll("button")]
    .find((b: any) => b.textContent?.trim() === label) as any;
function click(node: any): void {
  act(() => {
    node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
}
/** Open the "Learn offsets automatically" disclosure — Start lives inside it,
 *  so every assertion below is vacuous until this has actually opened. */
function openLearn(): void {
  const d = [...container.querySelectorAll("button")]
    .find((b: any) => /Learn offsets automatically/.test(b.textContent ?? "")) as any;
  assert(d != null, "no learn disclosure — canLearn did not reach the modal");
  if (d.getAttribute("aria-expanded") !== "true") click(d);
  assert(d.getAttribute("aria-expanded") === "true", "the learn disclosure did not open");
}

// ------------------------------------------------------------- blocked Start
render(BLOCKED);
await test("the learn panel is open and Start is on screen (precondition)", () => {
  openLearn();
  assert(named("Start") != null,
    "no Start button — every assertion about its blocked state would be vacuous");
  assert(/Reference/.test(text()),
    "the reference picker is missing, so this is not the panel under test");
});

await test("the blocked Start states its reason in visible text", () => {
  assert(text().includes(`Unavailable — ${BLOCKED}`),
    `the reason reaches nobody but a mouse hover. Panel text: ${text().slice(-400)}`);
});

await test("the blocked Start is announced as disabled but stays reachable", () => {
  const start = named("Start");
  assert(start.getAttribute("aria-disabled") === "true",
    "Start does not announce itself as unavailable");
  assert(!start.hasAttribute("disabled"),
    "Start uses the native `disabled` attribute, which takes the control AND " +
    "its reason out of the accessibility tree (house rule §11.8)");
});

await test("pressing the blocked Start starts nothing", () => {
  learnCalls = 0;
  click(named("Start"));
  assert(learnCalls === 0, "a blocked Start ran the learn anyway");
});

await test("pressing the blocked Start answers instead of doing nothing at all", () => {
  // The visible line is always there for a sighted user; a PRESS must also be
  // acknowledged, which is the half `title=` could never deliver on touch.
  const alert = container.querySelector('[role="alert"]') as any;
  assert(alert != null,
    "the press produced no announcement at all — a blocked button whose press " +
    "does nothing is exactly the defect this control was cited for");
  assert(alert.textContent.includes(BLOCKED),
    `the announcement does not name the blocker: ${alert.textContent}`);
});

// ------------------------------------------------------------- live Start
await test("with nothing blocking it, Start is live and says nothing about being blocked", () => {
  render(null);
  openLearn();
  const start = named("Start");
  assert(start != null, "Start disappeared when it became usable");
  assert(start.getAttribute("aria-disabled") == null,
    "a usable Start still announces itself as disabled");
  assert(!/Unavailable/.test(text()),
    "the blocked-reason line is still on screen under a live button");
  learnCalls = 0;
  click(start);
  assert(learnCalls === 1, `a live Start ran the learn ${learnCalls} times, expected 1`);
});

// ------------------------------------------------------------------ report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`filterNamesModal: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
