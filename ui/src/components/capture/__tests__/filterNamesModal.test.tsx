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
const { useStore } = await import("../../../store");

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
let lastLearn: any = null;
let lastSave: any = null;
/** every POST the modal made, so "the Stop button stops something" is a claim
 *  about the wire and not about a label. */
const posts: { path: string; body: any }[] = [];
win.fetch = async (url: any, init: any = {}) => {
  if ((init.method ?? "GET").toUpperCase() === "POST") {
    posts.push({ path: String(url), body: init.body ? JSON.parse(init.body) : null });
  }
  return {
    ok: true, status: 200, headers: { get: () => "application/json" },
    json: async () => ({}), text: async () => "{}",
  };
};
Object.defineProperty(g, "fetch", { value: win.fetch, writable: true, configurable: true });

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
      onLearn: async (_ref: number, req: any) => { learnCalls++; lastLearn = req; },
      onSave: async (...a: any[]) => { lastSave = a; },
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

// ------------------------------------------- #148 narrowband slots (2026-08-08)
// The offsets run measured L/R/G/B at one exposure and could not focus S, Ha
// or Oiii at all. A 3-7 nm passband is the same star tens of times fainter, so
// those slots need their own exposure and gain — and the marking belongs on
// the wheel, not on the run, because a wheel does not change between nights.

const nbBox = (i: number) =>
  container.querySelector(`[aria-label="Slot ${i + 1} is a narrowband filter"]`) as any;
const field = (label: string) =>
  container.querySelector(`[aria-label="${label}"]`) as any;
function typeInto(el: any, value: string): void {
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(
      win.HTMLInputElement.prototype, "value")!.set!;
    setter.call(el, value);
    el.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
}

await test("every slot can be marked narrowband, and the marking is saved with the wheel", async () => {
  render(null);
  assert(nbBox(0) != null, "no narrowband tick on slot 1 — the multi-select never rendered");
  click(nbBox(2));            // G, for the sake of argument
  const save = named("Save");
  assert(save != null, "no Save button");
  click(save);
  await act(async () => { await Promise.resolve(); });
  assert(lastSave != null, "Save never called through");
  assert(Array.isArray(lastSave[3]),
    "Save did not carry a narrowband list — the tick would die with the dialog");
  assert(lastSave[3][2] === true && lastSave[3][0] === false,
    `the wrong slot was saved as narrowband: ${JSON.stringify(lastSave[3])}`);
});

await test("a narrowband slot is swept at its own exposure, x4 the broadband one", () => {
  render(null);
  openLearn();
  const exp = field("Sweep exposure seconds");
  const gain = field("Sweep gain");
  assert(exp != null && gain != null,
    "the learn panel has no sweep exposure/gain fields — the run would use the "
    + "server's 2s/120 defaults, which is the bug under this one");
  typeInto(exp, "10");
  typeInto(gain, "300");
  click(nbBox(1));            // R, standing in for Ha
  const nbExp = field("Narrowband sweep exposure seconds");
  assert(nbExp != null, "no narrowband exposure field once a slot is marked");
  assert(nbExp.value === "40",
    `the narrowband exposure did not follow the sweep exposure: ${nbExp.value}`);
  click(named("Start"));
  assert(lastLearn != null, "Start sent no settings at all");
  assert(lastLearn.exposure_s === 10 && lastLearn.gain === 300,
    `the sweep settings on screen were not the ones sent: ${JSON.stringify(lastLearn)}`);
  assert(lastLearn.narrowband[1] === true && lastLearn.narrowband[0] === false,
    `the narrowband marking was not sent: ${JSON.stringify(lastLearn.narrowband)}`);
  assert(lastLearn.nb_exposure_s === 40,
    `narrowband exposure should be x4 of 10s, got ${lastLearn.nb_exposure_s}`);
  assert(lastLearn.nb_gain === 300, `narrowband gain: ${lastLearn.nb_gain}`);
});

await test("a hand-typed narrowband exposure wins over the derived one", () => {
  render(null);
  openLearn();
  typeInto(field("Sweep exposure seconds"), "10");
  click(nbBox(1));
  typeInto(field("Narrowband sweep exposure seconds"), "90");
  click(named("Start"));
  assert(lastLearn.nb_exposure_s === 90,
    `the typed narrowband exposure was ignored: ${lastLearn.nb_exposure_s}`);
});

// --------------------------------------------- #184 the run had no way out
await test("a run in flight can be stopped, and the stop reaches the rig", async () => {
  render(null);
  openLearn();
  assert(named("Stop") == null,
    "a Stop button is offered with nothing running — it would report a "
    + "cancel of nothing as success");
  await act(async () => {
    useStore.setState({
      filterOffsetsLearn: { state: "running", slot: 1, of: 4, name: "R" },
    } as never);
  });
  const stop = named("Stop");
  assert(stop != null,
    "a filter-offsets run is in flight and there is no way to stop it — "
    + "before this the only exit was restarting the server");
  posts.length = 0;
  click(stop);
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  assert(posts.some((p) => p.path.includes("/api/filterwheel/learn-offsets/cancel")),
    `Stop posted nothing to the cancel route: ${posts.map((p) => p.path).join("|")}`);
  await act(async () => { useStore.setState({ filterOffsetsLearn: null } as never); });
});

// ------------------------------------------------------------------ report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`filterNamesModal: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
